import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Signal } from "@/lib/api/types";

const dashboard = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));

vi.mock("@/lib/dashboard-context", () => ({ useDashboard: () => dashboard.current }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: () => {}, push: () => {} }) }));
vi.mock("@/lib/api/session", () => ({ useSession: () => ({ config: { quotes: false } }) }));
vi.mock("@/lib/live-quotes", () => ({ useLiveQuotes: () => ({ status: "disabled", quotes: new Map() }) }));
vi.mock("./use-stock-overview", () => ({ useStockOverview: () => ({ data: null, error: null, loading: true }) }));
vi.mock("./price-chart-card", () => ({ PriceChartCard: () => <div data-testid="chart" /> }));
vi.mock("./profile/profile-section", () => ({ StockProfileSection: () => <div data-testid="profile" /> }));
vi.mock("./quick-stock-panel", () => ({ QuickStockPanel: () => <div data-testid="quick-panel" /> }));
vi.mock("@/features/terminal/ai-rail", () => ({
  AiRail: (props: { showSignal?: boolean; variant?: string }) => (
    <aside data-testid="ai-rail" data-show-signal={String(props.showSignal)} data-variant={props.variant} />
  ),
}));
vi.mock("@/components/as-of-control", () => ({ AsOfControl: () => <div data-testid="asof" /> }));
vi.mock("@/components/watchlist-star", () => ({ WatchlistStar: () => <button data-testid="star" /> }));

import { StockPage } from "./stock-page";

const SIGNAL: Signal = {
  symbol: "2330.TW",
  as_of: "2026-11-27",
  strategy_version: "v1",
  score: 71,
  action: "increase",
  regime: "risk_on",
  confidence: 0.6,
  factors: { trend: 80, momentum: 70, volume: 60, relative_strength: 65, risk_quality: 55 },
  alerts: [],
  data_available_at: "2026-11-27T10:00:00Z",
  observations: 500,
} as unknown as Signal;

function setDashboard(over: { symbol?: string; signal?: Signal | null; loading?: boolean; syncWait?: unknown; error?: string | null }) {
  dashboard.current = {
    query: { symbol: over.symbol ?? "2330.TW", asOf: "2026-11-27", benchmark: "^TWII" },
    error: over.error ?? null,
    refresh: () => {},
    instrumentName: { symbol: "2330.TW", name: "台積電" },
    signalData: {
      signal: over.signal === undefined ? SIGNAL : over.signal,
      marketEnvironment: null,
      chipFlow: null,
      loading: over.loading ?? false,
      syncWait: over.syncWait ?? null,
      cancelSync: () => {},
    },
  };
}

const html = (symbol: string | null) => renderToStaticMarkup(<StockPage symbol={symbol} />);

describe("StockPage states", () => {
  beforeEach(() => setDashboard({}));

  it("explains an unknown code and points to the screener", () => {
    const out = html(null);
    expect(out).toContain("查無此股票代號");
    expect(out).toContain('href="/screener"');
    expect(out).toContain("⌘K");
  });

  it("looks a bare code up instead of guessing its market", () => {
    const out = renderToStaticMarkup(<StockPage symbol={null} bareCode="6488" />);
    expect(out).toContain("正在開啟 6488");
    expect(out).not.toContain("查無此股票代號");
  });

  it("shows header, section nav, verdict, rail and every section when ready", () => {
    const out = html("2330.TW");
    expect(out).toContain("台積電");
    expect(out).toContain(">2330<");
    expect(out).toContain("上市");
    for (const id of ["summary", "chart", "evidence", "profile", "chips"]) {
      expect(out).toContain(`id="${id}"`);
      expect(out).toContain(`href="#${id}"`);
    }
    expect(out).toContain('data-show-signal="false"');
    expect(out).toContain('data-variant="page"');
    expect(out).toContain("下一步");
    expect(out).toContain("回測這檔");
    expect(out).not.toContain("用歷史資料檢驗策略");
  });

  it("links the header actions", () => {
    const out = html("6488.TWO");
    expect(out).toContain("上櫃");
    expect(out).toContain("/backtest?symbol=6488.TWO");
    expect(out).toContain("/forecast?symbol=6488.TWO");
    expect(out).toContain("/watchlist?view=terminal");
  });

  it("never shows another stock's verdict under the new name", () => {
    setDashboard({ symbol: "2330.TW", signal: SIGNAL, loading: true });
    const out = html("2317.TW");
    expect(out).toContain("正在計算訊號");
    expect(out).not.toContain("hero-action");
    expect(out).toContain('data-testid="ai-rail"');
  });

  it("dims instead of replacing while the same stock refreshes", () => {
    setDashboard({ loading: true });
    const out = html("2330.TW");
    expect(out).toContain("is-refreshing");
    expect(out).toContain("hero-action");
  });

  it("falls back to the quick panel, under the header, when there is no signal", () => {
    setDashboard({ signal: null, error: "boom" });
    const out = html("2330.TW");
    expect(out).toContain('data-testid="quick-panel"');
    expect(out).toContain("stock-header");
    expect(out).toContain('data-testid="ai-rail"');
    expect(out).not.toContain('data-testid="chart"');
  });

  it("shows a skeleton while the first signal loads", () => {
    setDashboard({ signal: null, loading: true });
    const out = html("2330.TW");
    expect(out).toContain("正在計算訊號");
    expect(out).not.toContain('data-testid="quick-panel"');
  });
});
