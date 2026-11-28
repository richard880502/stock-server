import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { MarketDerivatives } from "@/lib/api/profile";
import type { ScreenerMeta, ScreenerResponse, SectorRotation, WatchlistItem } from "@/lib/api/types";
import { CandidateRows } from "./candidates-card";
import { DerivativesBody, EnvironmentBody } from "./market-cards";
import { PaperBody } from "./paper-card";
import { SectorBody } from "./sectors-card";
import { WatchlistBody } from "./watchlist-card";

const html = (node: React.ReactElement) => renderToStaticMarkup(node);
const noop = () => {};

const item = (symbol: string, name: string, over: Partial<WatchlistItem> = {}): WatchlistItem =>
  ({ symbol, name, market: "TW", close: 100, day_change_pct: -3.59, recent_closes: [98, 99, 100], ...over }) as WatchlistItem;

describe("watchlist card", () => {
  const base = { message: null, items: null, count: 0, recent: [], onRetry: noop };
  it("shows rows linking to the stock page", () => {
    const out = html(<WatchlistBody {...base} phase="ready" items={[item("2330.TW", "台積電")]} count={1} />);
    expect(out).toContain('href="/stock/2330.TW"');
    expect(out).toContain("−3.59%");
  });
  it("empty list: pointer to 選股 and the ⌘K hint", () => {
    const out = html(<WatchlistBody {...base} phase="ready" items={[]} />);
    expect(out).toContain("還沒有自選股");
    expect(out).toContain('href="/screener"');
    expect(out).toContain("⌘K");
  });
  it("unavailable: lists recent symbols instead", () => {
    const out = html(<WatchlistBody {...base} phase="unavailable" recent={[{ symbol: "2317.TW", name: "鴻海" }]} />);
    expect(out).toContain('href="/stock/2317.TW"');
    expect(out).toContain("Google");
  });
  it("loading is a skeleton and error has a retry", () => {
    expect(html(<WatchlistBody {...base} phase="loading" />)).toContain("skeleton");
    expect(html(<WatchlistBody {...base} phase="error" message="壞了" />)).toContain("重試");
  });
});

describe("paper card", () => {
  it("no account: pitch and the start button", () => {
    const out = html(<PaperBody summary={null} />);
    expect(out).toContain("開始模擬交易");
    expect(out).toContain('href="/paper"');
  });
  it("account with missing numbers shows dashes", () => {
    const out = html(
      <PaperBody summary={{ id: "1", name: "主", accountCount: 1, equity: 1_000_000, dayPnl: null, positions: null, workingOrders: 2 }} />,
    );
    expect(out).toContain("1,000,000");
    expect(out).toContain("— 檔 / 2 筆");
    expect(out).toContain("前往模擬交易");
  });
});

describe("derivatives card", () => {
  it("missing fields render as dashes, no payload days says so", () => {
    const sparse = { days: [{ date: "2026-11-27", vix: 51.94 }] } as unknown as MarketDerivatives;
    const out = html(<DerivativesBody data={sparse} />);
    expect(out).toContain("51.94");
    expect(out).toContain("尚無資料");
    expect(html(<DerivativesBody data={{ days: [] } as unknown as MarketDerivatives} />)).toContain("尚未就緒");
  });
});

describe("environment card", () => {
  it("renders score, date and an alert; tolerates no alerts field", () => {
    const env = {
      market: "TW",
      as_of: "2026-11-27",
      score: 41.2,
      regime: "neutral",
      alerts: [{ code: "x", severity: "high", title: "量能急縮", evidence: {} }],
    } as never;
    const out = html(<EnvironmentBody environment={env} />);
    expect(out).toContain("41");
    expect(out).toContain("2026-11-27");
    expect(out).toContain("量能急縮");
    expect(html(<EnvironmentBody environment={{ ...(env as object), alerts: undefined } as never} />)).not.toContain("今日警示");
  });
  it("shows breadth, MA20 share and foreign flow; dashes for missing fields; no latest = score only", () => {
    const env = { market: "TW", as_of: "2026-11-27", score: 38.66, regime: "risk_off", alerts: [] };
    const latest = { total_issues: 1086, advancing_issues: 439, declining_issues: 550, above_ma20_issues: 149, foreign_net_flow: -29628066640 };
    const full = html(<EnvironmentBody environment={{ ...env, latest } as never} />);
    expect(full).toContain("上漲 439 · 下跌 550");
    expect(full).toContain("13.7%");
    expect(full).toContain("149 / 1,086 檔");
    expect(full).toContain("−296.3 億");
    expect(full).toContain("today-ratio");
    const sparse = html(<EnvironmentBody environment={{ ...env, latest: {} } as never} />);
    expect(sparse).toContain("—");
    expect(sparse).not.toContain("NaN");
    expect(sparse).not.toContain("today-ratio");
    const none = html(<EnvironmentBody environment={env as never} />);
    expect(none).not.toContain("漲跌家數");
    expect(none).toContain("39");
  });
});

describe("candidates", () => {
  const meta = {
    factors: [],
    metrics: [{ key: "ret_20d", label: "20 日報酬", short: "20日", unit: "%", higher_is_better: null }],
    presets: [{ key: "momentum", label: "強勢動能", description: "", sort: "momentum", order: "desc", filters: [], columns: ["momentum", "ret_20d"] }],
  } as unknown as ScreenerMeta;
  const data = (over: Partial<ScreenerResponse>): ScreenerResponse =>
    ({ entries: [], snapshot_pending: false, notice: null, ...over }) as unknown as ScreenerResponse;
  it("rows link to the stock page and show the supporting metric", () => {
    const out = html(
      <CandidateRows
        presetKey="momentum"
        meta={meta}
        data={data({
          entries: [{ symbol: "3293.TWO", name: "鈊象", composite: 92.81, scores: {}, metrics: { ret_20d: 2.68 } } as never],
        })}
      />,
    );
    expect(out).toContain('href="/stock/3293.TWO"');
    expect(out).toContain("93");
    expect(out).toContain("+2.7%");
  });
  it("shows the server notice and an empty message", () => {
    const out = html(<CandidateRows presetKey="momentum" meta={meta} data={data({ notice: "日期超出範圍" })} />);
    expect(out).toContain("日期超出範圍");
    expect(out).toContain("沒有符合");
  });
});

describe("sectors", () => {
  it("lists strongest and weakest, or a waiting note", () => {
    const row = (code: string, name: string, ret: number) => ({ code, name, returns: { "5": { equal_pct: ret, weighted_pct: null } } });
    const out = html(
      <SectorBody data={{ sectors: [row("a", "半導體", 5), row("b", "航運", -4)] } as unknown as SectorRotation} />,
    );
    expect(out).toContain("半導體");
    expect(out).toContain("航運");
    expect(html(<SectorBody data={{ sectors: [] } as unknown as SectorRotation} />)).toContain("尚未就緒");
  });
});
