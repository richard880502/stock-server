"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_QUANT_API_URL ?? "http://127.0.0.1:18100";

type Alert = {
  code: string;
  severity: "info" | "medium" | "high";
  title: string;
  evidence: Record<string, number | string>;
};

type Signal = {
  symbol: string;
  as_of: string;
  strategy_version: string;
  score: number;
  action: "increase" | "hold" | "reduce" | "avoid";
  regime: "risk_on" | "neutral" | "risk_off";
  confidence: number;
  factors: {
    trend: number;
    momentum: number;
    volume: number;
    relative_strength: number;
    risk_quality: number;
  };
  alerts: Alert[];
  data_available_at: string;
  observations: number;
};

type MarketEnvironment = {
  market: string;
  as_of: string;
  strategy_version: string;
  score: number;
  regime: "risk_on" | "neutral" | "risk_off";
  confidence: number;
  factors: {
    participation: number;
    advance_decline: number;
    breadth_momentum: number;
    volume_breadth: number;
    institutional_flow: number;
    leverage_quality: number;
  };
  alerts: Alert[];
  latest: {
    total_issues: number;
    advancing_issues: number;
    declining_issues: number;
    above_ma20_issues: number;
    foreign_net_flow: number;
    investment_trust_net_flow: number;
    dealer_net_flow: number;
    futures_net_open_interest: number;
    margin_balance: number;
    turnover_value: number;
    source: string;
  };
  data_available_at: string;
  observations: number;
};

type ChipFlow = {
  symbol: string;
  as_of: string;
  score: number;
  confidence: number;
  institutional_observations: number;
  branch_observations: number;
  branch_data_available: boolean;
  branch_data_status: string;
  metrics: {
    foreign_net_5d: number;
    investment_trust_net_5d: number;
    dealer_net_5d: number;
    total_net_20d: number;
    flow_volume_ratio_5d: number;
    foreign_streak: number;
    investment_trust_streak: number;
    foreign_holding_ratio: number | null;
    foreign_holding_change_5d: number | null;
    branch_buy_concentration_5d: number | null;
    branch_sell_concentration_5d: number | null;
    branch_estimated_cost: number | null;
    branch_day_trade_ratio: number | null;
  };
  top_buyers: {
    branch_code: string;
    branch_name: string;
    net_shares: number;
    estimated_cost: number | null;
    win_rate: number | null;
  }[];
  top_sellers: {
    branch_code: string;
    branch_name: string;
    net_shares: number;
    estimated_cost: number | null;
    win_rate: number | null;
  }[];
  alerts: Alert[];
};

type LlmStatus = {
  enabled: boolean;
  configured: boolean;
  missing: string[];
  model: string | null;
  base_url: string;
};

type DataStatus = {
  real_data_ready: boolean;
  bar_series: {
    key: string;
    source: string;
    first_date: string;
    latest_date: string;
    observations: number;
    adjusted_observations: number;
    is_real: boolean;
  }[];
  market_series: {
    key: string;
    source: string;
    first_date: string;
    latest_date: string;
    observations: number;
    is_real: boolean;
  }[];
};

type Judgement = {
  headline: string;
  stance: "bullish" | "neutral" | "cautious" | "bearish";
  operation_guide: string;
  key_evidence: string[];
  risk_warnings: string[];
  scenarios: { name: string; condition: string; response: string }[];
  limitations: string[];
};

type LlmReport = {
  model: string;
  prompt_version: string;
  judgement: Judgement;
};

type DebateCase = {
  stance: "bullish" | "bearish";
  thesis: string;
  key_evidence: string[];
  counterpoints_to_address: string[];
};

type DebateReport = {
  model: string;
  prompt_version: string;
  bull_case: DebateCase;
  bear_case: DebateCase;
  judgement: Judgement;
};

type LlmStreamPayload = {
  text?: string;
  model?: string;
  message?: string;
};

type BacktestJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  error: string | null;
  result: null | {
    observations: number;
    trades: number;
    total_return: number;
    annualized_return: number;
    annualized_volatility: number;
    sharpe: number | null;
    max_drawdown: number;
    equity_curve: { trading_date: string; equity: number; position: number }[];
  };
};

const factorLabels: Record<keyof Signal["factors"], string> = {
  trend: "趨勢結構",
  momentum: "價格動能",
  volume: "量能確認",
  relative_strength: "相對強弱",
  risk_quality: "風險品質",
};

const marketFactorLabels: Record<
  keyof MarketEnvironment["factors"],
  string
> = {
  participation: "均線參與度",
  advance_decline: "漲跌家數",
  breadth_momentum: "創高創低",
  volume_breadth: "量能廣度",
  institutional_flow: "法人資金環境",
  leverage_quality: "槓桿品質",
};

const actionCopy: Record<Signal["action"], { label: string; guide: string }> = {
  increase: {
    label: "偏多配置",
    guide: "訊號支持偏多，但仍應等待價格確認並依風險承受度調整曝險。",
  },
  hold: {
    label: "維持觀察",
    guide: "多空證據尚未形成壓倒性方向，保留部位並等待條件確認。",
  },
  reduce: {
    label: "降低風險",
    guide: "環境轉弱，優先控制曝險，避免在波動擴張時增加部位。",
  },
  avoid: {
    label: "暫停進場",
    guide: "目前風險報酬不具優勢，等待訊號回到可接受區間。",
  },
};

const regimeCopy: Record<Signal["regime"], string> = {
  risk_on: "Risk-on",
  neutral: "Neutral",
  risk_off: "Risk-off",
};

const today = () => {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
};

const formatPct = (value: number) =>
  new Intl.NumberFormat("zh-TW", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);

const formatNumber = (value: number, digits = 1) =>
  new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);

const AUTH_STORAGE_KEY = "quant_signal_api_key";

// Set once after a successful login; read by every fetch call below. A
// module-level variable (not React state) so fetchJson doesn't need the key
// threaded through every call site.
let authApiKey: string | null = null;

function withAuthHeaders(init?: RequestInit): RequestInit {
  if (!authApiKey) return init ?? {};
  return {
    ...init,
    headers: { ...(init?.headers ?? {}), Authorization: `Bearer ${authApiKey}` },
  };
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withAuthHeaders(init));
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      payload?.detail ?? `服務回應異常（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return payload as T;
}

function Dashboard({
  apiKey,
  onLogout,
}: {
  apiKey: string;
  onLogout: () => void;
}) {
  useEffect(() => {
    authApiKey = apiKey;
  }, [apiKey]);

  const [symbol, setSymbol] = useState("2330.TW");
  const [benchmark, setBenchmark] = useState("^TWII");
  const [asOf, setAsOf] = useState(today);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [marketEnvironment, setMarketEnvironment] =
    useState<MarketEnvironment | null>(null);
  const [chipFlow, setChipFlow] = useState<ChipFlow | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [llmReport, setLlmReport] = useState<LlmReport | null>(null);
  const [streamedGuide, setStreamedGuide] = useState("");
  const [streamModel, setStreamModel] = useState<string | null>(null);
  const [debateReport, setDebateReport] = useState<DebateReport | null>(null);
  const [loadingSignal, setLoadingSignal] = useState(false);
  const [loadingLlm, setLoadingLlm] = useState(false);
  const [loadingDebate, setLoadingDebate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"signal" | "backtest">("signal");

  const [backtestStart, setBacktestStart] = useState("2025-01-01");
  const [entryScore, setEntryScore] = useState(70);
  const [exitScore, setExitScore] = useState(45);
  const [backtest, setBacktest] = useState<BacktestJob | null>(null);
  const [loadingBacktest, setLoadingBacktest] = useState(false);
  const [useChipFilter, setUseChipFilter] = useState(false);
  const selectedMarket =
    symbol.trim().toUpperCase().endsWith(".TWO") ||
    symbol.trim().toUpperCase() === "^TWOII"
      ? "TWO"
      : "TW";

  const loadSignal = useCallback(async () => {
    setLoadingSignal(true);
    setError(null);
    setLlmReport(null);
    setStreamedGuide("");
    setStreamModel(null);
    setBacktest(null);
    try {
      const params = new URLSearchParams({ as_of: asOf });
      if (benchmark.trim()) params.set("benchmark", benchmark.trim());
      const [nextSignal, nextMarketEnvironment, nextChipFlow] = await Promise.all([
        fetchJson<Signal>(
          `${API_URL}/api/v1/signals/${encodeURIComponent(symbol.trim())}?${params}`,
        ),
        fetchJson<MarketEnvironment>(
          `${API_URL}/api/v1/market-environments/${selectedMarket}?as_of=${encodeURIComponent(asOf)}`,
        ),
        fetchJson<ChipFlow>(
          `${API_URL}/api/v1/chip-flows/${encodeURIComponent(symbol.trim())}?as_of=${encodeURIComponent(asOf)}`,
        ).catch(() => null),
      ]);
      setSignal(nextSignal);
      setMarketEnvironment(nextMarketEnvironment);
      setChipFlow(nextChipFlow);
    } catch (nextError) {
      setSignal(null);
      setMarketEnvironment(null);
      setChipFlow(null);
      setError(
        nextError instanceof Error ? nextError.message : "無法取得量化訊號",
      );
    } finally {
      setLoadingSignal(false);
    }
  }, [asOf, benchmark, selectedMarket, symbol]);

  useEffect(() => {
    fetchJson<LlmStatus>(`${API_URL}/api/v1/llm/status`)
      .then(setLlmStatus)
      .catch(() => setLlmStatus(null));
    fetchJson<DataStatus>(`${API_URL}/api/v1/data/status`)
      .then(setDataStatus)
      .catch(() => setDataStatus(null));

    const initialParams = new URLSearchParams({
      as_of: today(),
      benchmark: "^TWII",
    });
    Promise.all([
      fetchJson<Signal>(
        `${API_URL}/api/v1/signals/2330.TW?${initialParams}`,
      ),
      fetchJson<MarketEnvironment>(
        `${API_URL}/api/v1/market-environments/TW?as_of=${encodeURIComponent(today())}`,
      ),
      fetchJson<ChipFlow>(
        `${API_URL}/api/v1/chip-flows/2330.TW?as_of=${encodeURIComponent(today())}`,
      ).catch(() => null),
    ])
      .then(([initialSignal, initialMarket, initialChipFlow]) => {
        setSignal(initialSignal);
        setMarketEnvironment(initialMarket);
        setChipFlow(initialChipFlow);
      })
      .catch((initialError) =>
        setError(
          initialError instanceof Error
            ? initialError.message
            : "無法取得量化訊號",
        ),
      )
      .finally(() => setLoadingSignal(false));
  }, []);

  const runLlmAnalysis = async () => {
    setLoadingLlm(true);
    setError(null);
    setLlmReport(null);
    setStreamedGuide("");
    setStreamModel(null);
    try {
      const response = await fetch(
        `${API_URL}/api/v1/llm-analyses/stream`,
        withAuthHeaders({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: symbol.trim(),
            as_of: asOf,
            market: selectedMarket,
            benchmark: benchmark.trim() || null,
            strategy_version: "regime_v1",
            backtest_job_id:
              backtest?.status === "completed" ? backtest.id : null,
          }),
        }),
      );
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => null);
        throw new Error(
          payload?.detail ?? `服務回應異常（HTTP ${response.status}）`,
        );
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamError: Error | null = null;

      const processEvent = (block: string) => {
        const event = block
          .split("\n")
          .find((line) => line.startsWith("event:"))
          ?.slice("event:".length)
          .trim();
        const dataLine = block
          .split("\n")
          .find((line) => line.startsWith("data:"));
        if (!event || !dataLine) return;
        const payload = JSON.parse(
          dataLine.slice("data:".length).trim(),
        ) as LlmStreamPayload;
        if (event === "meta" && payload.model) setStreamModel(payload.model);
        if (event === "delta" && payload.text) {
          setStreamedGuide((current) => current + payload.text);
        }
        if (event === "error") {
          streamError = new Error(payload.message ?? "LLM 串流分析失敗");
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          processEvent(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
      if (streamError) throw streamError;
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "LLM 分析失敗",
      );
    } finally {
      setLoadingLlm(false);
    }
  };

  const runDebateAnalysis = async () => {
    setLoadingDebate(true);
    setError(null);
    setDebateReport(null);
    try {
      const report = await fetchJson<DebateReport>(
        `${API_URL}/api/v1/debate-analyses`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: symbol.trim(),
            as_of: asOf,
            market: selectedMarket,
            benchmark: benchmark.trim() || null,
            strategy_version: "regime_v1",
            backtest_job_id:
              backtest?.status === "completed" ? backtest.id : null,
          }),
        },
      );
      setDebateReport(report);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "多空辯論分析失敗",
      );
    } finally {
      setLoadingDebate(false);
    }
  };

  const pollBacktest = async (jobId: string) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const job = await fetchJson<BacktestJob>(
        `${API_URL}/api/v1/backtests/${jobId}`,
      );
      setBacktest(job);
      if (job.status === "completed" || job.status === "failed") return;
      await new Promise((resolve) => window.setTimeout(resolve, 1_000));
    }
    throw new Error("回測仍在執行，請稍後再查詢");
  };

  const runBacktest = async (event: FormEvent) => {
    event.preventDefault();
    setLoadingBacktest(true);
    setBacktest(null);
    setError(null);
    try {
      const job = await fetchJson<BacktestJob>(
        `${API_URL}/api/v1/backtests`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: symbol.trim(),
            benchmark: benchmark.trim() || null,
            market: selectedMarket,
            strategy_version: "regime_v1",
            start: backtestStart,
            end: asOf,
            entry_score: entryScore,
            exit_score: exitScore,
            fee_bps: 5,
            slippage_bps: 5,
            initial_capital: 1_000_000,
            use_chip_filter: useChipFilter,
            chip_entry_score: 55,
            chip_exit_score: 40,
          }),
        },
      );
      setBacktest(job);
      await pollBacktest(job.id);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "無法完成回測",
      );
    } finally {
      setLoadingBacktest(false);
    }
  };

  const equityBars = useMemo(() => {
    const points = backtest?.result?.equity_curve ?? [];
    if (!points.length) return [];
    const sampled = points.filter(
      (_, index) => index % Math.max(1, Math.floor(points.length / 42)) === 0,
    );
    const values = sampled.map((point) => point.equity);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const spread = maximum - minimum || 1;
    return sampled.map((point) => ({
      ...point,
      height: 18 + ((point.equity - minimum) / spread) * 62,
    }));
  }, [backtest]);

  const action = signal ? actionCopy[signal.action] : null;
  const combinedAlerts: Alert[] = [
    ...(marketEnvironment?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `market:${alert.code}`,
      title: `市場｜${alert.title}`,
    })),
    ...(signal?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `symbol:${alert.code}`,
      title: `個股｜${alert.title}`,
    })),
    ...(chipFlow?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `chip:${alert.code}`,
      title: `籌碼｜${alert.title}`,
    })),
  ];

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#" aria-label="Quant Signal 首頁">
          <span className="brand-mark">QS</span>
          <span>
            <strong>Quant Signal</strong>
            <small>Evidence-first market intelligence</small>
          </span>
        </a>
        <div className="service-state">
          <span className="live-dot" />
          本機量化服務
          <span className="service-separator" />
          資料 {dataStatus?.real_data_ready ? "交易所官方" : "示範"}
          <span className="service-separator" />
          LLM {llmStatus?.configured ? "已連線" : "待設定"}
          <span className="service-separator" />
          <button className="logout-link" onClick={onLogout}>
            登出
          </button>
        </div>
      </header>

      <section className="workspace">
        <div className="intro-row">
          <div>
            <p className="eyebrow">DAILY DECISION DESK</p>
            <h1>把市場雜訊，整理成可驗證的決策條件。</h1>
          </div>
          <p className="intro-copy">
            量化模型負責計算，LLM 負責解讀。每一個操作觀點，都保留原始因子、
            異常證據與歷史回測。
          </p>
        </div>

        <form
          className="query-bar"
          onSubmit={(event) => {
            event.preventDefault();
            loadSignal();
          }}
        >
          <label>
            <span>分析標的</span>
            <input
              value={symbol}
              onChange={(event) => {
                const nextSymbol = event.target.value.toUpperCase();
                setSymbol(nextSymbol);
                if (nextSymbol.endsWith(".TWO") && benchmark === "^TWII") {
                  setBenchmark("^TWOII");
                } else if (
                  nextSymbol.endsWith(".TW") &&
                  benchmark === "^TWOII"
                ) {
                  setBenchmark("^TWII");
                }
              }}
              placeholder="2330.TW / 6488.TWO"
              aria-label="分析標的"
            />
          </label>
          <label>
            <span>比較基準</span>
            <input
              value={benchmark}
              onChange={(event) =>
                setBenchmark(event.target.value.toUpperCase())
              }
              placeholder="^TWII"
              aria-label="比較基準"
            />
          </label>
          <label>
            <span>資料截止日</span>
            <input
              type="date"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              aria-label="資料截止日"
            />
          </label>
          <button className="primary-button" disabled={loadingSignal}>
            {loadingSignal ? "計算中…" : "更新訊號"}
          </button>
        </form>

        {error && (
          <div className="error-banner" role="alert">
            <strong>目前無法完成這項操作</strong>
            <span>{error}</span>
            <button onClick={() => setError(null)} aria-label="關閉錯誤訊息">
              ×
            </button>
          </div>
        )}

        <nav className="view-tabs" aria-label="控制台檢視">
          <button
            className={activeView === "signal" ? "active" : ""}
            onClick={() => setActiveView("signal")}
          >
            今日訊號
          </button>
          <button
            className={activeView === "backtest" ? "active" : ""}
            onClick={() => setActiveView("backtest")}
          >
            策略回測
          </button>
        </nav>

        {activeView === "signal" ? (
          <>
            <section className="market-panel">
              <div className="market-score">
                <div>
                  <p className="eyebrow">MARKET ENVIRONMENT</p>
                  <span className="market-name">
                    {selectedMarket === "TWO" ? "上櫃市場 · TWO" : "上市市場 · TW"} ·{" "}
                    {marketEnvironment?.latest.source === "twse_official" ||
                    marketEnvironment?.latest.source === "tpex_official"
                      ? "交易所官方資料"
                      : "示範資料"}
                  </span>
                </div>
                <strong>
                  {marketEnvironment
                    ? formatNumber(marketEnvironment.score, 0)
                    : "—"}
                </strong>
                <span
                  className={`regime ${marketEnvironment?.regime ?? "neutral"}`}
                >
                  {marketEnvironment
                    ? regimeCopy[marketEnvironment.regime]
                    : "—"}
                </span>
              </div>
              <div className="market-factor-grid">
                {marketEnvironment
                  ? (
                      Object.entries(marketEnvironment.factors) as [
                        keyof MarketEnvironment["factors"],
                        number,
                      ][]
                    ).map(([key, value]) => (
                      <div key={key}>
                        <span>{marketFactorLabels[key]}</span>
                        <strong>{formatNumber(value, 0)}</strong>
                        <i>
                          <b style={{ width: `${value}%` }} />
                        </i>
                      </div>
                    ))
                  : Object.values(marketFactorLabels).map((label) => (
                      <div className="muted" key={label}>
                        <span>{label}</span>
                        <strong>—</strong>
                        <i />
                      </div>
                    ))}
              </div>
              <div className="market-tape">
                <div>
                  <span>上漲／下跌</span>
                  <strong>
                    {marketEnvironment
                      ? `${marketEnvironment.latest.advancing_issues} / ${marketEnvironment.latest.declining_issues}`
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>MA20 以上</span>
                  <strong>
                    {marketEnvironment
                      ? formatPct(
                          marketEnvironment.latest.above_ma20_issues /
                            marketEnvironment.latest.total_issues,
                        )
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>法人合計</span>
                  <strong>
                    {marketEnvironment
                      ? `${formatNumber(
                          (marketEnvironment.latest.foreign_net_flow +
                            marketEnvironment.latest
                              .investment_trust_net_flow +
                            marketEnvironment.latest.dealer_net_flow) /
                            100_000_000,
                          1,
                        )} 億`
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>期貨淨部位</span>
                  <strong>
                    {marketEnvironment?.latest.source === "twse_official" ||
                    marketEnvironment?.latest.source === "tpex_official"
                      ? "待串 TAIFEX"
                      : marketEnvironment
                      ? formatNumber(
                          marketEnvironment.latest.futures_net_open_interest,
                          0,
                        )
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>市場異常</span>
                  <strong>{marketEnvironment?.alerts.length ?? 0}</strong>
                </div>
              </div>
            </section>

            <section className="signal-grid">
              <article className="signal-card score-card">
                <div className="card-heading">
                  <span>個股綜合訊號</span>
                  <span className={`regime ${signal?.regime ?? "neutral"}`}>
                    {signal ? regimeCopy[signal.regime] : "—"}
                  </span>
                </div>
                {signal ? (
                  <>
                    <div className="score-block">
                      <div
                        className="score-ring"
                        style={
                          {
                            "--score": `${signal.score * 3.6}deg`,
                          } as React.CSSProperties
                        }
                      >
                        <div>
                          <strong>{Math.round(signal.score)}</strong>
                          <span>/ 100</span>
                        </div>
                      </div>
                      <div className="score-copy">
                        <p>{signal.symbol}</p>
                        <h2>{action?.label}</h2>
                        <span>
                          信心度 {formatPct(signal.confidence)} ·{" "}
                          {signal.observations} 筆觀察
                        </span>
                      </div>
                    </div>
                    <div className="quant-guide">
                      <span>量化操作框架</span>
                      <p>{action?.guide}</p>
                    </div>
                  </>
                ) : (
                  <div className="empty-state">輸入標的後取得訊號</div>
                )}
              </article>

              <article className="signal-card factors-card">
                <div className="card-heading">
                  <span>因子證據</span>
                  <small>regime_v1</small>
                </div>
                <div className="factor-list">
                  {signal
                    ? (
                        Object.entries(signal.factors) as [
                          keyof Signal["factors"],
                          number,
                        ][]
                      ).map(([key, value]) => (
                        <div className="factor-row" key={key}>
                          <div>
                            <span>{factorLabels[key]}</span>
                            <strong>{formatNumber(value, 0)}</strong>
                          </div>
                          <div className="factor-track">
                            <span style={{ width: `${value}%` }} />
                          </div>
                        </div>
                      ))
                    : Object.values(factorLabels).map((label) => (
                        <div className="factor-row muted" key={label}>
                          <div>
                            <span>{label}</span>
                            <strong>—</strong>
                          </div>
                          <div className="factor-track" />
                        </div>
                      ))}
                </div>
              </article>

              <article className="signal-card alerts-card">
                <div className="card-heading">
                  <span>異常雷達</span>
                  <span className="alert-count">
                    {combinedAlerts.length}
                  </span>
                </div>
                {combinedAlerts.length ? (
                  <div className="alert-list">
                    {combinedAlerts.map((alert) => (
                      <div className={`alert-item ${alert.severity}`} key={alert.code}>
                        <span className="alert-indicator" />
                        <div>
                          <strong>{alert.title}</strong>
                          <p>
                            {Object.entries(alert.evidence)
                              .map(([key, value]) => `${key}: ${value}`)
                              .join(" · ")}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="clear-state">
                    <span>✓</span>
                    <div>
                      <strong>未偵測到重大異常</strong>
                      <p>市場寬度、資金流與個股量價目前都在可接受範圍。</p>
                    </div>
                  </div>
                )}
              </article>
            </section>

            <section className="chip-panel">
              <div className="chip-summary">
                <div>
                  <p className="eyebrow">STOCK CHIP FLOW</p>
                  <h2>法人與分點籌碼</h2>
                  <span>
                    {chipFlow
                      ? `${chipFlow.institutional_observations} 個法人交易日`
                      : "尚未匯入足夠的個股法人資料"}
                  </span>
                </div>
                <strong>{chipFlow ? formatNumber(chipFlow.score, 0) : "—"}</strong>
                <small>籌碼分數</small>
              </div>
              <div className="chip-metrics">
                <div>
                  <span>外資 5 日</span>
                  <strong>{chipFlow ? formatNumber(chipFlow.metrics.foreign_net_5d / 1000, 0) : "—"}</strong>
                  <small>張 · 連續 {chipFlow?.metrics.foreign_streak ?? "—"} 日</small>
                </div>
                <div>
                  <span>投信 5 日</span>
                  <strong>{chipFlow ? formatNumber(chipFlow.metrics.investment_trust_net_5d / 1000, 0) : "—"}</strong>
                  <small>張 · 連續 {chipFlow?.metrics.investment_trust_streak ?? "—"} 日</small>
                </div>
                <div>
                  <span>自營商 5 日</span>
                  <strong>{chipFlow ? formatNumber(chipFlow.metrics.dealer_net_5d / 1000, 0) : "—"}</strong>
                  <small>張</small>
                </div>
                <div>
                  <span>外資持股</span>
                  <strong>
                    {chipFlow?.metrics.foreign_holding_ratio != null
                      ? `${formatNumber(chipFlow.metrics.foreign_holding_ratio, 2)}%`
                      : "—"}
                  </strong>
                  <small>
                    5 日變化{" "}
                    {chipFlow?.metrics.foreign_holding_change_5d != null
                      ? `${formatNumber(chipFlow.metrics.foreign_holding_change_5d, 2)} pp`
                      : "待資料"}
                  </small>
                </div>
              </div>
              <div className="branch-status">
                <div>
                  <span className={chipFlow?.branch_data_available ? "connected" : ""}>
                    {chipFlow?.branch_data_available ? "已載入授權分點資料" : "分點資料尚未授權／匯入"}
                  </span>
                  <p>
                    {chipFlow?.branch_data_available
                      ? `共 ${chipFlow.branch_observations} 筆，可計算集中度、成本、短線活動與分點勝率。`
                      : "三大法人訊號照常運作；取得 TWSE／TPEx 或供應商授權檔後即可啟用分點分析。"}
                  </p>
                </div>
                <div className="branch-kpis">
                  <span>
                    買方集中{" "}
                    <b>{chipFlow?.metrics.branch_buy_concentration_5d != null ? formatPct(chipFlow.metrics.branch_buy_concentration_5d) : "—"}</b>
                  </span>
                  <span>
                    平均成本{" "}
                    <b>{chipFlow?.metrics.branch_estimated_cost != null ? formatNumber(chipFlow.metrics.branch_estimated_cost, 2) : "—"}</b>
                  </span>
                  <span>
                    短線比率{" "}
                    <b>{chipFlow?.metrics.branch_day_trade_ratio != null ? formatPct(chipFlow.metrics.branch_day_trade_ratio) : "—"}</b>
                  </span>
                </div>
              </div>
            </section>

            <section className="llm-panel">
              <div className="llm-header">
                <div>
                  <p className="eyebrow">LLM ANALYST</p>
                  <h2>讓模型解讀證據，而不是發明證據。</h2>
                </div>
                <div className="llm-actions">
                  <span className={llmStatus?.configured ? "connected" : ""}>
                    {llmStatus?.configured
                      ? llmStatus.model
                      : "OpenAI-compatible model 未設定"}
                  </span>
                  <button
                    className="secondary-button"
                    onClick={runLlmAnalysis}
                    disabled={!signal || loadingLlm || !llmStatus?.configured}
                  >
                    {loadingLlm ? "正在產生…" : "串流操作指南"}
                  </button>
                </div>
              </div>

              {llmReport ? (
                <div className="llm-result">
                  <div className="llm-summary">
                    <span className={`stance ${llmReport.judgement.stance}`}>
                      {llmReport.judgement.stance}
                    </span>
                    <h3>{llmReport.judgement.headline}</h3>
                    <p>{llmReport.judgement.operation_guide}</p>
                  </div>
                  <div className="evidence-columns">
                    <div>
                      <span className="column-label">關鍵證據</span>
                      <ul>
                        {llmReport.judgement.key_evidence.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <span className="column-label warning">風險與限制</span>
                      <ul>
                        {[
                          ...llmReport.judgement.risk_warnings,
                          ...llmReport.judgement.limitations,
                        ].map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                  {llmReport.judgement.scenarios.length > 0 && (
                    <div className="scenario-row">
                      {llmReport.judgement.scenarios.map((scenario) => (
                        <div key={scenario.name}>
                          <strong>{scenario.name}</strong>
                          <span>若 {scenario.condition}</span>
                          <p>{scenario.response}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : streamedGuide || loadingLlm ? (
                <div className="llm-stream-result" aria-live="polite">
                  <div className="stream-heading">
                    <span className="stream-pulse" />
                    <span>即時操作指南</span>
                    <small>{streamModel ?? llmStatus?.model ?? "準備分析上下文"}</small>
                  </div>
                  <p className={loadingLlm ? "is-streaming" : ""}>
                    {streamedGuide || "正在整合量化證據…"}
                  </p>
                </div>
              ) : (
                <div className="llm-empty">
                  <div className="prompt-preview">
                    <span>分析上下文</span>
                    <code>個股訊號</code>
                    <b>＋</b>
                    <code>市場寬度與資金</code>
                    <b>＋</b>
                    <code>法人與分點籌碼</code>
                    <b>＋</b>
                    <code>異常證據</code>
                    <b>＋</b>
                    <code>
                      {backtest?.status === "completed"
                        ? "已完成回測"
                        : "回測（選配）"}
                    </code>
                  </div>
                  <p>
                    {llmStatus?.configured
                      ? "量化證據已準備完成，可交由模型產生條件式操作指南。"
                      : "在後端設定 LLM 後，這裡會產生繁體中文的情境分析與風險提醒。"}
                  </p>
                </div>
              )}
            </section>

            <section className="llm-panel debate-panel">
              <div className="llm-header">
                <div>
                  <p className="eyebrow">BULL / BEAR DEBATE</p>
                  <h2>多空各自舉證，再由裁決者給出操作指南。</h2>
                </div>
                <div className="llm-actions">
                  <span className={llmStatus?.configured ? "connected" : ""}>
                    {llmStatus?.configured
                      ? llmStatus.model
                      : "OpenAI-compatible model 未設定"}
                  </span>
                  <button
                    className="secondary-button"
                    onClick={runDebateAnalysis}
                    disabled={!signal || loadingDebate || !llmStatus?.configured}
                  >
                    {loadingDebate ? "辯論中…" : "啟動多空辯論"}
                  </button>
                </div>
              </div>

              {debateReport ? (
                <div className="llm-result">
                  <div className="debate-columns">
                    <div className="debate-case bull">
                      <span className="stance bullish">多方</span>
                      <p>{debateReport.bull_case.thesis}</p>
                      <span className="column-label">關鍵證據</span>
                      <ul>
                        {debateReport.bull_case.key_evidence.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                      <span className="column-label warning">預期空方反駁</span>
                      <ul>
                        {debateReport.bull_case.counterpoints_to_address.map(
                          (item) => (
                            <li key={item}>{item}</li>
                          ),
                        )}
                      </ul>
                    </div>
                    <div className="debate-case bear">
                      <span className="stance bearish">空方</span>
                      <p>{debateReport.bear_case.thesis}</p>
                      <span className="column-label">關鍵證據</span>
                      <ul>
                        {debateReport.bear_case.key_evidence.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                      <span className="column-label warning">預期多方反駁</span>
                      <ul>
                        {debateReport.bear_case.counterpoints_to_address.map(
                          (item) => (
                            <li key={item}>{item}</li>
                          ),
                        )}
                      </ul>
                    </div>
                  </div>

                  <div className="llm-summary judgement-summary">
                    <span className={`stance ${debateReport.judgement.stance}`}>
                      {debateReport.judgement.stance}
                    </span>
                    <h3>{debateReport.judgement.headline}</h3>
                    <p>{debateReport.judgement.operation_guide}</p>
                  </div>
                  <div className="evidence-columns">
                    <div>
                      <span className="column-label">裁決證據</span>
                      <ul>
                        {debateReport.judgement.key_evidence.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <span className="column-label warning">風險與限制</span>
                      <ul>
                        {[
                          ...debateReport.judgement.risk_warnings,
                          ...debateReport.judgement.limitations,
                        ].map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                  {debateReport.judgement.scenarios.length > 0 && (
                    <div className="scenario-row">
                      {debateReport.judgement.scenarios.map((scenario) => (
                        <div key={scenario.name}>
                          <strong>{scenario.name}</strong>
                          <span>若 {scenario.condition}</span>
                          <p>{scenario.response}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="llm-empty">
                  <p>
                    {llmStatus?.configured
                      ? "按下「啟動多空辯論」，多方與空方研究員會各自基於同一份證據舉證，再由裁決者給出最終操作指南。"
                      : "在後端設定 LLM 後，這裡會顯示多空辯論與最終操作指南。"}
                  </p>
                </div>
              )}
            </section>
          </>
        ) : (
          <section className="backtest-layout">
            <form className="backtest-form" onSubmit={runBacktest}>
              <div>
                <p className="eyebrow">POINT-IN-TIME BACKTEST</p>
                <h2>驗證訊號，不替歷史找答案。</h2>
                <p>
                  以前一日收盤資訊產生訊號，下一交易日開盤成交，並計入費用與滑價。
                </p>
              </div>
              <label>
                <span>回測起始日</span>
                <input
                  type="date"
                  value={backtestStart}
                  max={asOf}
                  onChange={(event) => setBacktestStart(event.target.value)}
                />
              </label>
              <div className="threshold-row">
                <label>
                  <span>進場分數</span>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={entryScore}
                    onChange={(event) => setEntryScore(Number(event.target.value))}
                  />
                </label>
                <label>
                  <span>退出分數</span>
                  <input
                    type="number"
                    min="0"
                    max="99"
                    value={exitScore}
                    onChange={(event) => setExitScore(Number(event.target.value))}
                  />
                </label>
              </div>
              <div className="cost-note">
                交易假設：5 bps 費用＋5 bps 滑價，初始資金 NT$1,000,000
              </div>
              <label className="chip-filter-toggle">
                <input
                  type="checkbox"
                  checked={useChipFilter}
                  onChange={(event) => setUseChipFilter(event.target.checked)}
                />
                <span>
                  啟用籌碼濾網
                  <small>籌碼分數 ≥55 才進場，低於 40 退出</small>
                </span>
              </label>
              <button
                className="primary-button full"
                disabled={loadingBacktest}
              >
                {loadingBacktest ? "正在執行回測…" : "啟動策略回測"}
              </button>
            </form>

            <div className="backtest-result">
              <div className="card-heading">
                <span>回測結果</span>
                {backtest && (
                  <span className={`job-status ${backtest.status}`}>
                    {backtest.status}
                  </span>
                )}
              </div>
              {backtest?.result ? (
                <>
                  <div className="metric-grid">
                    <div>
                      <span>總報酬</span>
                      <strong
                        className={
                          backtest.result.total_return >= 0 ? "positive" : "negative"
                        }
                      >
                        {formatPct(backtest.result.total_return)}
                      </strong>
                    </div>
                    <div>
                      <span>年化報酬</span>
                      <strong>{formatPct(backtest.result.annualized_return)}</strong>
                    </div>
                    <div>
                      <span>最大回撤</span>
                      <strong className="negative">
                        {formatPct(backtest.result.max_drawdown)}
                      </strong>
                    </div>
                    <div>
                      <span>Sharpe</span>
                      <strong>
                        {backtest.result.sharpe === null
                          ? "—"
                          : formatNumber(backtest.result.sharpe, 2)}
                      </strong>
                    </div>
                  </div>
                  <div className="equity-chart" aria-label="回測淨值簡圖">
                    {equityBars.map((point) => (
                      <span
                        key={point.trading_date}
                        style={{ height: `${point.height}%` }}
                        title={`${point.trading_date}: ${formatNumber(point.equity, 0)}`}
                      />
                    ))}
                  </div>
                  <div className="backtest-foot">
                    <span>{backtest.result.observations} 個交易日</span>
                    <span>{backtest.result.trades} 次換倉</span>
                    <span>
                      年化波動 {formatPct(backtest.result.annualized_volatility)}
                    </span>
                  </div>
                </>
              ) : (
                <div className="empty-backtest">
                  <span>↗</span>
                  <h3>等待策略驗證</h3>
                  <p>設定期間與門檻後啟動回測，結果會由獨立 worker 計算。</p>
                </div>
              )}
            </div>
          </section>
        )}

        <footer>
          <span>Quant Signal Server · Research environment</span>
          <span>
            市場資料時間：
            {marketEnvironment?.data_available_at
              .replace("T", " ")
              .slice(0, 16) ?? "—"}
          </span>
          <span>研究工具，不構成投資建議</span>
        </footer>
      </section>
    </main>
  );
}

export default function Home() {
  const [apiKey, setApiKey] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : window.localStorage.getItem(AUTH_STORAGE_KEY),
  );
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);

  useEffect(() => {
    authApiKey = apiKey;
  }, [apiKey]);

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoggingIn(true);
    setLoginError(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(payload?.detail ?? "密碼錯誤");
      }
      const key = payload.api_key as string;
      window.localStorage.setItem(AUTH_STORAGE_KEY, key);
      authApiKey = key;
      setApiKey(key);
      setPassword("");
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "登入失敗");
    } finally {
      setLoggingIn(false);
    }
  };

  const handleLogout = () => {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    authApiKey = null;
    setApiKey(null);
  };

  if (!apiKey) {
    return (
      <main className="login-screen">
        <form className="login-card" onSubmit={handleLogin}>
          <span className="brand-mark">QS</span>
          <h1>Quant Signal</h1>
          <p>輸入密碼以進入研究控制台。</p>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="密碼"
            autoFocus
          />
          {loginError && <span className="login-error">{loginError}</span>}
          <button className="primary-button" disabled={loggingIn || !password}>
            {loggingIn ? "登入中…" : "登入"}
          </button>
        </form>
      </main>
    );
  }

  return <Dashboard apiKey={apiKey} onLogout={handleLogout} />;
}
