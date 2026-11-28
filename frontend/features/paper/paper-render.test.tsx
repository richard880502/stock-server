import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountHeader } from "./account-header";
import { NewAccountForm } from "./account-forms";
import { EquityCard } from "./equity-card";
import { ConfirmPanel, EstimateBlock, RulesDialog } from "./order-estimate";
import { FillsTable, OrdersTable } from "./orders-panel";
import {
  ACCOUNT_MAIN,
  ACCOUNT_SECOND,
  DETAIL_MAIN,
  EQUITY_POINTS,
  FILL_BUY,
  FILL_SELL,
  ORDER_FILLED,
  ORDER_REJECTED,
  ORDER_WORKING,
  ORDERS_DONE,
  POSITION_LOSS_DELAYED,
  PREVIEW_NEXT_SESSION,
  PREVIEW_OK,
  PREVIEW_REJECTED,
  PREVIEW_SELL,
  RULES,
} from "./paper-fixtures";
import { PositionsTable } from "./positions-table";

const noop = () => {};
const html = (node: React.ReactElement) => renderToStaticMarkup(node);

describe("account header", () => {
  const markup = html(
    <AccountHeader
      accounts={[ACCOUNT_MAIN, ACCOUNT_SECOND]}
      maxAccounts={3}
      account={ACCOUNT_MAIN}
      detail={DETAIL_MAIN}
      stale={false}
      actions={{ onSelect: noop, onNew: noop, onSettings: noop, onReset: noop, onArchive: noop }}
    />,
  );

  it("shows the 模擬 badge, the four figures and the price time", () => {
    expect(markup).toContain("模擬");
    for (const label of ["總權益", "今日損益", "累計損益", "可用資金"]) expect(markup).toContain(label);
    expect(markup).toContain("1,013,845");
    expect(markup).toContain("−1,200");
    expect(markup).toContain("−0.12%");
    expect(markup).toContain("+13,845");
    expect(markup).toContain("+1.38%");
    expect(markup).toContain("價格時間 13:24:55");
  });

  it("tones profit red and loss green via the shared classes", () => {
    expect(markup).toMatch(/paper-figure text-down[^>]*>[\s\S]*今日損益/);
    expect(markup).toMatch(/paper-figure text-up[^>]*>[\s\S]*累計損益/);
  });
});

describe("positions table", () => {
  const markup = html(
    <PositionsTable positions={DETAIL_MAIN.positions} loading={false} stale={false} onTrade={noop} />,
  );

  it("links each holding to its stock page", () => {
    expect(markup).toMatch(/<a [^>]*href="\/stock\/[0-9A-Z.]+"[^>]*class="paper-link"|<a [^>]*class="paper-link"[^>]*href="\/stock\/[0-9A-Z.]+"/);
  });

  it("is a real table with the contract's columns", () => {
    expect(markup).toContain("<table");
    for (const head of ["股票", "張數", "均價", "現價", "今日", "市值", "未實現損益", "佔比"]) expect(markup).toContain(head);
  });

  it("shows 可賣 only when it differs, the 延遲 tag only for delayed prices, and tones the loss", () => {
    expect(markup).toContain("可賣 1");
    expect(markup.match(/class="paper-delay"/g)?.length).toBe(1);
    expect(markup).toContain("−14,500");
    expect(markup).toContain("−3.52%");
    expect(markup).toContain(POSITION_LOSS_DELAYED.name);
    expect(markup).toContain("加碼");
  });

  it("has an empty state and a skeleton state", () => {
    expect(html(<PositionsTable positions={[]} loading={false} stale={false} onTrade={noop} />)).toContain("還沒有持股");
    expect(html(<PositionsTable positions={null} loading stale={false} onTrade={noop} />)).toContain("skeleton");
  });
});

describe("orders and fills", () => {
  it("lists working orders with a cancel button", () => {
    const markup = html(<OrdersTable orders={[ORDER_WORKING]} mode="working" onCancel={noop} />);
    expect(markup).toContain("限價 1,085");
    expect(markup).toContain("取消");
    expect(markup).toContain("11/27");
  });

  it("shows status chips and the reject reason for done orders", () => {
    const markup = html(<OrdersTable orders={ORDERS_DONE} mode="done" />);
    expect(markup).toContain("已成交");
    expect(markup).toContain("已取消");
    expect(markup).toContain("已拒絕");
    expect(markup).toContain(ORDER_REJECTED.reject_reason as string);
    expect(markup).toContain("市價");
    expect(ORDER_FILLED.fill).not.toBeNull();
  });

  it("shows fills with costs, realised P&L and a price-source label", () => {
    const markup = html(<FillsTable fills={[FILL_BUY, FILL_SELL]} names={new Map([["2330.TW", "台積電"]])} />);
    expect(markup).toContain("1,532");
    expect(markup).toContain("1,260");
    expect(markup).toContain("+6,141");
    expect(markup).toContain("逐筆");
    expect(markup).toContain("分K");
    expect(markup).toContain("台積電");
  });
});

describe("estimate block", () => {
  const base = { stale: false, loading: false, error: null, incomplete: false };

  it("renders a buy estimate with reserve and warnings, announced politely", () => {
    const markup = html(<EstimateBlock {...base} preview={PREVIEW_OK} />);
    expect(markup).toContain('aria-live="polite"');
    for (const label of ["預估成交價", "預估金額", "手續費", "交易稅", "合計", "圈存金額", "下單後可用資金"]) expect(markup).toContain(label);
    expect(markup).toContain("NT$ 1,091,553");
    expect(markup).toContain("20 日均量");
  });

  it("shows 預估入帳 instead of 圈存 for a sell", () => {
    const markup = html(<EstimateBlock {...base} preview={PREVIEW_SELL} />);
    expect(markup).toContain("預估入帳");
    expect(markup).not.toContain("圈存金額");
  });

  it("says which session a queued order waits for", () => {
    const markup = html(<EstimateBlock {...base} preview={PREVIEW_NEXT_SESSION} />);
    expect(markup).toContain("現在非交易時段，將排入 11/30 開盤後撮合");
  });

  it("shows the reject reason as an alert", () => {
    const markup = html(<EstimateBlock {...base} preview={PREVIEW_REJECTED} />);
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("可用資金不足");
  });

  it("guides when inputs are incomplete", () => {
    expect(html(<EstimateBlock {...base} preview={null} incomplete />)).toContain("選擇股票");
  });
});

describe("confirm step", () => {
  it("restates the order, carries the 模擬 badge and the no-real-order line", () => {
    const markup = html(
      <ConfirmPanel
        preview={PREVIEW_OK}
        body={{ symbol: "2330.TW", side: "buy", order_type: "limit", limit_price: 1085, lots: 1 }}
        accountName="主帳戶"
        submitting={false}
        error={null}
        onBack={noop}
        onConfirm={noop}
      />,
    );
    for (const text of ["確認委託", "模擬", "買進 台積電", "1 張", "限價 1,085", "預估合計", "確認送出", "返回修改", "模擬交易不會送出真實委託"])
      expect(markup).toContain(text);
  });
});

describe("rules dialog, forms and chart", () => {
  it("renders rules from the API with tick bands", () => {
    const markup = html(<RulesDialog open onClose={noop} rules={RULES} loading={false} error={null} discount={0.6} />);
    expect(markup).toContain("撮合規則");
    expect(markup).toContain("6 折");
    expect(markup).toContain("0.1425%");
    expect(markup).toContain("1,000 以上");
  });

  it("first-run form has the defaults and quick picks", () => {
    const markup = html(<NewAccountForm onSubmit={noop} busy={false} error={null} />);
    for (const text of ["主帳戶", "1000000", "50 萬", "100 萬", "300 萬", "1,000 萬", "不打折", "6 折", "2.8 折", "允許現股當沖", "建立模擬帳戶"])
      expect(markup).toContain(text);
  });

  it("equity card shows the empty state under two points and the chart otherwise", () => {
    const empty = html(<EquityCard points={[EQUITY_POINTS[0]]} loading={false} stale={false} range="3m" onRange={noop} />);
    expect(empty).toContain("明天起這裡會畫出你的權益曲線");
    // a compact one-line state: no tall chart placeholder, no range switch
    expect(empty).toContain("paper-equity-compact");
    expect(empty).not.toContain("paper-chart-empty");
    expect(empty).not.toContain("paper-chart-skeleton");
    expect(empty).not.toContain("曲線區間");
    const none = html(<EquityCard points={[]} loading={false} stale={false} range="3m" onRange={noop} />);
    expect(none).toContain("paper-equity-compact");
    const full = html(<EquityCard points={EQUITY_POINTS} loading={false} stale={false} range="1m" onRange={noop} />);
    expect(full).not.toContain("明天起");
    expect(full).toContain("1 月");
    expect(full).toContain("全部");
  });
});
