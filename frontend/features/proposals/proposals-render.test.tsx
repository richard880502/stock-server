import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ALL_PROPOSALS,
  PROPOSAL_ACCEPTED,
  PROPOSAL_BLOCKED,
  PROPOSAL_BUY,
  PROPOSAL_EXPIRED,
  PROPOSAL_HOLD,
  PROPOSAL_MISALIGNED,
  PROPOSAL_REJECTED,
  PROPOSAL_SELL,
  PROPOSAL_SPARSE,
  PROPOSAL_SUPERSEDED,
} from "./fixtures";
import { OrderTicket, type TicketPrefill } from "@/features/paper/order-ticket";
import { ProposalCard } from "./proposal-card";
import { ProposalHistoryRow } from "./proposals-panel";
import { RailProposalView, type RailProposalView as RailView } from "./rail-proposal";

const html = (node: React.ReactElement) => renderToStaticMarkup(node);
const card = (proposal: Parameters<typeof ProposalCard>[0]["proposal"]) => html(<ProposalCard proposal={proposal} />);

describe("ProposalCard", () => {
  it("renders every fixture without throwing, always with the disclaimer and a backtest link", () => {
    for (const proposal of ALL_PROPOSALS) {
      const out = card(proposal);
      expect(out).toContain("不構成投資建議");
      expect(out).toContain("先用回測驗證");
      expect(out).toContain(`/backtest?symbol=${encodeURIComponent(proposal.symbol)}`);
      expect(out).toContain("模擬");
    }
  });

  it("shows the plan with distances from the reference price", () => {
    const out = card(PROPOSAL_BUY);
    for (const label of ["限價", "張數", "預估金額", "停損", "停利", "觀察天數"]) expect(out).toContain(label);
    expect(out).toContain("2,175");
    expect(out).toContain("−4.1%"); // stop 2090 vs 2180
    expect(out).toContain("+6.4%"); // target 2320 vs 2180
    expect(out).toContain("20 天");
    expect(out).toContain("買進");
    expect(out).toContain("帶入模擬下單");
    expect(out).toContain("/paper?proposal=p-buy");
    expect(out).toContain("不採用");
  });

  it("shows adjusted checks above the fold, not inside a disclosure", () => {
    const out = card(PROPOSAL_BUY);
    const checks = out.indexOf("系統檢查");
    const details = out.indexOf("<details");
    expect(checks).toBeGreaterThan(-1);
    expect(checks).toBeLessThan(details);
    expect(out).toContain("系統已調整");
    expect(out).toContain("已調整為 2175");
  });

  it("puts blocked checks first and disables the order button with the reason", () => {
    const out = card(PROPOSAL_BLOCKED);
    expect(out).toContain("無法照提案下單");
    expect(out.indexOf("無法照提案下單")).toBeLessThan(out.indexOf("系統已調整"));
    expect(out.indexOf("系統已調整")).toBeLessThan(out.indexOf("使用最近收盤資料"));
    expect(out).toMatch(/<button[^>]*disabled[^>]*>帶入模擬下單/);
    expect(out).toContain("系統檢查未通過");
  });

  it("warns when the AI contradicts the quant signal", () => {
    expect(card(PROPOSAL_MISALIGNED)).toContain("與量化訊號不一致");
    expect(card(PROPOSAL_MISALIGNED)).toContain("暫停進場");
    expect(card(PROPOSAL_BUY)).not.toContain("與量化訊號不一致");
    expect(card(PROPOSAL_BUY)).toContain("偏多配置");
  });

  it("shows sizing and the largest possible loss", () => {
    const out = card(PROPOSAL_BUY);
    expect(out).toContain("部位與風險");
    expect(out).toContain("21.0%");
    expect(out).toContain("NT$ 85,000");
    expect(out).toContain("8.5%");
    expect(out).toContain("NT$ 640,000");
    // no stop, no loss figure
    expect(card(PROPOSAL_SELL)).toContain("未設停損，無法估算");
  });

  it("keeps rationale, evidence, risks and invalidation in a disclosure", () => {
    const out = card(PROPOSAL_BUY);
    expect(out).toContain("<details");
    for (const text of ["法人連三日買超", "外資連三日買超", "大盤轉弱", "收盤跌破 2090"]) expect(out).toContain(text);
  });

  it("offers only 收起 for hold, with no order button", () => {
    const out = card(PROPOSAL_HOLD);
    expect(out).toContain("觀望");
    expect(out).toContain("AI 建議觀望，沒有委託可帶入");
    expect(out).toContain("收起");
    expect(out).not.toContain("帶入模擬下單");
  });

  it("states the outcome of decided proposals and offers no actions", () => {
    expect(card(PROPOSAL_ACCEPTED)).toContain("已帶入模擬委託");
    expect(card(PROPOSAL_REJECTED)).toContain("未採用：價位太高");
    expect(card(PROPOSAL_EXPIRED)).toContain("已過期");
    expect(card(PROPOSAL_SUPERSEDED)).toContain("已被較新的提案取代");
    for (const proposal of [PROPOSAL_ACCEPTED, PROPOSAL_REJECTED, PROPOSAL_EXPIRED, PROPOSAL_SUPERSEDED]) {
      expect(card(proposal)).not.toContain("帶入模擬下單");
    }
  });

  it("copes with a sparse proposal: dashes, no crash, no order button", () => {
    const out = card(PROPOSAL_SPARSE);
    expect(out).toContain("2317");
    expect(out).toContain("—");
    expect(out).toContain("帶入模擬下單");
    expect(out).toMatch(/<button[^>]*disabled[^>]*>帶入模擬下單/);
    expect(out).not.toContain("NaN");
    expect(out).not.toContain("undefined");
    expect(out).not.toContain("null");
  });

  it("uses the callback instead of a link on the paper page", () => {
    const out = html(<ProposalCard proposal={PROPOSAL_BUY} onUse={() => {}} />);
    expect(out).not.toContain("/paper?proposal=");
    expect(out).toContain("帶入模擬下單");
  });
});

describe("history row", () => {
  it("shows status, stock, action and a plan summary", () => {
    const out = html(<ul><ProposalHistoryRow proposal={PROPOSAL_ACCEPTED} /></ul>);
    expect(out).toContain("已帶入委託");
    expect(out).toContain("台積電");
    expect(out).toContain("買進");
    expect(out).toContain("限價 2,175");
  });
});

const view = (patch: Partial<RailView> = {}): RailView => ({
  phase: "ready",
  symbol: "2330.TW",
  accountName: "主帳戶",
  accountCount: 1,
  proposal: null,
  creating: false,
  wait: null,
  error: null,
  disabled: false,
  onCreate: () => {},
  onCancelWait: () => {},
  onChange: () => {},
  ...patch,
});

describe("RailProposalView", () => {
  it("hides the whole block while accounts are unknown or the user is not a Google sign-in", () => {
    expect(html(<RailProposalView {...view({ phase: "hidden" })} />)).toBe("");
  });

  it("replaces the button with a link to create an account", () => {
    const out = html(<RailProposalView {...view({ phase: "no-account", accountName: null })} />);
    expect(out).toContain("先建立模擬帳戶");
    expect(out).toContain('href="/paper"');
    expect(out).not.toContain("產生交易提案");
  });

  it("shows the button, the explanation and the call-the-model notice", () => {
    const out = html(<RailProposalView {...view()} />);
    expect(out).toContain("產生交易提案");
    expect(out).toContain("須由你確認才會成為模擬委託");
    expect(out).toContain("按下會呼叫模型");
    expect(out).not.toContain("使用模擬帳戶");
  });

  it("names the account when there are several", () => {
    expect(html(<RailProposalView {...view({ accountCount: 3 })} />)).toContain("使用模擬帳戶：主帳戶");
  });

  it("shows a pending proposal for this stock and offers to regenerate it", () => {
    const out = html(<RailProposalView {...view({ proposal: PROPOSAL_BUY })} />);
    expect(out).toContain("回測後拉回的買點");
    expect(out).toContain("重新產生交易提案");
  });

  it("never shows a proposal that belongs to another stock", () => {
    const out = html(<RailProposalView {...view({ symbol: "2317.TW", proposal: PROPOSAL_BUY })} />);
    expect(out).not.toContain("回測後拉回的買點");
    expect(out).toContain("產生交易提案");
  });

  it("shows a skeleton while the model works and disables the button", () => {
    const out = html(<RailProposalView {...view({ creating: true })} />);
    expect(out).toContain("交易提案產生中");
    expect(out).toContain("產生提案中…");
    expect(out).toMatch(/<button[^>]*disabled/);
  });

  it("explains errors including the quota and a model outage", () => {
    const quota = html(<RailProposalView {...view({ error: { message: "今日使用次數已達上限，請稍後再試。", status: 429 } })} />);
    expect(quota).toContain("今日使用次數已達上限");
    const down = html(<RailProposalView {...view({ error: { message: "upstream exploded", status: 503 } })} />);
    expect(down).toContain("模型服務暫時無法回應");
  });
});

describe("order ticket from a proposal", () => {
  const account = {
    id: "acc-1", name: "主帳戶", initial_cash: 1_000_000, cash: 640_000, available_cash: 640_000,
    market_value: 0, equity: 640_000, unrealized_pnl: 0, realized_pnl: 0, total_pnl: 0, total_return_pct: 0,
    day_pnl: 0, day_return_pct: 0, positions_count: 0, working_orders: 0, commission_discount: 1,
    allow_day_trade: false, created_at: "2026-11-01T00:00:00Z", prices_as_of: null,
  };
  const ticket = (prefill: TicketPrefill | null) =>
    html(
      <OrderTicket
        account={account}
        positions={[]}
        rules={null}
        rulesLoading={false}
        rulesError={null}
        prefill={prefill}
        quote={undefined}
        recents={[]}
        onSymbolChange={() => {}}
        onPlaced={() => {}}
      />,
    );

  it("fills price and lots and shows the banner with a link back to the proposal", () => {
    const out = ticket({ nonce: 1, symbol: "2330.TW", side: "buy", lots: 2, limitPrice: 2175, proposalId: "p-buy" });
    expect(out).toContain("來自 AI 提案 · 你可以修改價格與張數");
    expect(out).toContain("#proposal-p-buy");
    expect(out).toContain('value="2175"');
  });

  it("shows no banner for an ordinary prefill", () => {
    expect(ticket({ nonce: 1, symbol: "2330.TW", side: "buy" })).not.toContain("來自 AI 提案");
  });
});
