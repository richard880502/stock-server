import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountHeader } from "./account-header";
import { NewAccountForm } from "./account-forms";
import { EquityCard } from "./equity-card";
import { ConfirmPanel, EstimateBlock, RulesDialog } from "./order-estimate";
import { CorporateActionsTable, DIVIDEND_NOTE, FillsTable, OrdersPanel, OrdersTable, TransfersTable } from "./orders-panel";
import { rebaseBenchmark } from "./paper-math";
import { ResetAmountField, TransferForm } from "./transfer-forms";
import { flowMarks } from "./transfer-math";
import {
  ACCOUNT_MAIN,
  ACTION_BOTH,
  ACTION_CASH,
  ACCOUNT_SECOND,
  DETAIL_AFTER_DEPOSIT,
  DETAIL_MAIN,
  DETAIL_WITH_DIVIDENDS,
  EQUITY_POINTS,
  EQUITY_POINTS_WITH_DEPOSIT,
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
  TRANSFERS,
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
      actions={{ onSelect: noop, onNew: noop, onSettings: noop, onReset: noop, onArchive: noop, onTransfer: noop }}
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

describe("corporate actions", () => {
  const markup = html(<CorporateActionsTable actions={[ACTION_BOTH, ACTION_CASH]} />);

  it("lists the ex-date, the stock as a link, the cash credited and the bonus shares", () => {
    expect(markup).toContain("11/27");
    expect(markup).toContain("現金股利入帳 NT$5,000");
    expect(markup).toContain("配股 +100 股");
    expect(markup).toMatch(/<a [^>]*href="\/stock\/2330\.TW"/);
    expect(markup).toContain("環球晶");
  });

  it("shows a dash where a kind of payout did not happen", () => {
    expect(markup).toContain("現金股利入帳 NT$3,000");
    expect(markup.match(/配股 \+/g)?.length).toBe(1);
    expect(markup).toContain('<td class="num">—</td>');
  });

  it("carries the simplification note text", () => {
    expect(DIVIDEND_NOTE).toContain("除息日");
    expect(DIVIDEND_NOTE).toContain("零股");
  });

  it("shows 累計股利 in the header only when dividends were received", () => {
    const props = {
      accounts: [ACCOUNT_MAIN],
      maxAccounts: 3,
      account: ACCOUNT_MAIN,
      stale: false,
      actions: { onSelect: noop, onNew: noop, onSettings: noop, onReset: noop, onArchive: noop, onTransfer: noop },
    };
    expect(html(<AccountHeader {...props} detail={DETAIL_WITH_DIVIDENDS} />)).toContain("累計股利");
    expect(html(<AccountHeader {...props} detail={DETAIL_MAIN} />)).not.toContain("累計股利");
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

  it("states the bond-ETF tax rule and the ETF price-limit limitation", () => {
    const markup = html(
      <RulesDialog
        open
        onClose={noop}
        rules={{ ...RULES, bond_etf_tax_exempt_through: "2026-12-31" }}
        loading={false}
        error={null}
        discount={1}
      />,
    );
    expect(markup).toContain("債券 ETF");
    expect(markup).toContain("2026-12-31");
    expect(markup).toContain("0050 實際有 ±10%");
    expect(markup).toContain("不會產生市場上沒有成交過的價格");
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

describe("入金／出金", () => {
  const props = {
    accounts: [ACCOUNT_MAIN],
    maxAccounts: 3,
    account: ACCOUNT_MAIN,
    stale: false,
    actions: { onSelect: noop, onNew: noop, onSettings: noop, onReset: noop, onArchive: noop, onTransfer: noop },
  };

  it("shows 投入資金 next to 總權益 only when it differs from the initial cash", () => {
    const withCapital = html(<AccountHeader {...props} detail={DETAIL_AFTER_DEPOSIT} />);
    expect(withCapital).toContain("投入資金");
    expect(withCapital).toContain("2,000,000");
    expect(html(<AccountHeader {...props} detail={DETAIL_MAIN} />)).not.toContain("投入資金");
    expect(html(<AccountHeader {...props} detail={{ ...DETAIL_MAIN, contributed_capital: 1_000_000 }} />)).not.toContain(
      "投入資金",
    );
  });

  it("explains the return figures as time-weighted in a tooltip", () => {
    const markup = html(<AccountHeader {...props} detail={DETAIL_MAIN} />);
    expect(markup.match(/title="時間加權報酬：入金不算獲利、出金不算虧損"/g)?.length).toBe(2);
  });

  const open = { name: "主帳戶", cash: 800_000, available_cash: 700_000 };
  const form = (initial?: Parameters<typeof TransferForm>[0]["initial"]) =>
    html(<TransferForm account={open} busy={false} error={null} onSubmit={noop} onCancel={noop} initial={initial} />);

  it("renders the deposit form with quick picks, the cash lines and the simulation note", () => {
    const markup = form();
    for (const text of ["入金", "出金", "10 萬", "50 萬", "100 萬", "500 萬", "目前現金", "可用資金", "入金後現金", "備註（選填）"])
      expect(markup).toContain(text);
    expect(markup).toContain("NT$ 800,000");
    expect(markup).toContain("NT$ 700,000");
    expect(markup).toContain("這是模擬資金，不會動到真實的錢。入金不算獲利、出金不算虧損。報酬率以時間加權計算：收盤後的入出金算在下一個交易日。");
    expect(markup).toMatch(/type="submit"[^>]*disabled/); // nothing entered yet
  });

  it("shows the resulting cash for a valid withdrawal and enables the button", () => {
    const markup = form({ direction: "out", amountText: "500000" });
    expect(markup).toContain("出金後現金");
    expect(markup).toContain("NT$ 300,000");
    expect(markup).toContain("確認出金");
    expect(markup).not.toMatch(/type="submit"[^>]*disabled/);
    expect(markup).not.toContain('role="alert"');
  });

  it("explains a withdrawal above the available cash", () => {
    const markup = form({ direction: "out", amountText: "750,000" });
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("出金不能超過可用資金 NT$ 700,000");
    expect(markup).toMatch(/type="submit"[^>]*disabled/);
  });

  it("explains a deposit that is out of range and carries a server error", () => {
    expect(form({ amountText: "500" })).toContain("金額必須介於 NT$ 1,000 到 NT$ 100,000,000 之間");
    const failed = html(
      <TransferForm account={open} busy={false} error="每個帳戶每天最多 20 筆入金／出金" onSubmit={noop} onCancel={noop} />,
    );
    expect(failed).toContain("每個帳戶每天最多 20 筆入金／出金");
    expect(html(<TransferForm account={open} busy error={null} onSubmit={noop} onCancel={noop} />)).toContain("處理中…");
  });

  it("lists transfers neutrally: time, 入金/出金, amount and note, no profit or loss tone", () => {
    const markup = html(<TransfersTable transfers={TRANSFERS} />);
    for (const text of ["資金異動", "時間", "類型", "金額", "備註", "入金", "出金", "NT$1,000,000", "NT$200,000", "年終獎金"])
      expect(markup).toContain(text);
    expect(markup).toContain("—"); // the withdrawal has no note
    expect(markup).not.toMatch(/text-(up|down)/);
  });

  it("adds the 資金異動 tab to the orders panel", () => {
    const markup = html(
      <OrdersPanel
        accountId="acc-main"
        workingHint={0}
        refreshToken={0}
        names={new Map()}
        onWorkingChanged={noop}
        onCancelled={noop}
      />,
    );
    expect(markup).toContain("資金異動");
  });

  it("says what the benchmark assumes, and the series carries the transfer days", () => {
    // The chart itself paints after measuring (the static render shows its skeleton), so
    // the markers are checked through the series and the pure helper that draws them.
    const markup = html(
      <EquityCard points={EQUITY_POINTS_WITH_DEPOSIT} loading={false} stale={false} range="1m" onRange={noop} />,
    );
    expect(markup).toContain("入金／出金視為同日買賣指數");
    const series = rebaseBenchmark(EQUITY_POINTS_WITH_DEPOSIT);
    expect(flowMarks(series.dates, series.flows).map((m) => m.label)).toEqual(["當日入金 NT$1,000,000"]);
    expect(flowMarks(rebaseBenchmark(EQUITY_POINTS).dates, rebaseBenchmark(EQUITY_POINTS).flows)).toEqual([]);
  });

  it("reset dialog: amount defaults to the current initial cash and says what changes", () => {
    const same = html(<ResetAmountField value="1000000" onChange={noop} current={1_000_000} />);
    for (const text of ["重設後的初始資金", "維持目前的初始資金 NT$ 1,000,000", "50 萬", "100 萬", "300 萬", "1,000 萬"])
      expect(same).toContain(text);
    expect(html(<ResetAmountField value="2000000" onChange={noop} current={1_000_000} />)).toContain(
      "改為 NT$ 2,000,000（原本 NT$ 1,000,000）",
    );
    expect(html(<ResetAmountField value="5" onChange={noop} current={1_000_000} />)).toContain("請輸入 100,000 到 100,000,000");
    // an unusual current amount gets its own pick
    expect(html(<ResetAmountField value="123000" onChange={noop} current={123_000} />)).toContain("12.3 萬");
  });

  it("the rules dialog explains transfers and time-weighted returns", () => {
    const markup = html(<RulesDialog open onClose={noop} rules={RULES} loading={false} error={null} discount={1} />);
    expect(markup).toContain("入金不算獲利、出金不算虧損");
    expect(markup).toContain("時間加權");
  });
});
